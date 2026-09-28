# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


























from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time

from .host_platform import remove_quietly, retry_file_op
from .logger import log, log_warning
from .policy import AGENT_HOME, ensure_agent_directories

RECORD_PREFIX = "process-"
RECORD_SUFFIX = ".json"

_LEGACY = ("last_call.json", "last_run.json")



MAX_AGE_S = 7 * 24 * 3600.0





STACK_PREFIX = "qgis-python-crash-info-"
OWN_PREFIX = "python-crash-"
OWN_SUFFIX = ".txt"
STACK_LIMIT = 2000
_STACK_READ_BYTES = 262_144

_record: dict = {}
_pending: dict = {}
_quit_signal = None


def args_digest(args) -> str:

    try:
        text = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        text = str(args)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _qgis_version() -> str:
    try:
        from qgis.core import Qgis

        return str(Qgis.QGIS_VERSION).split("-")[0]
    except Exception:  # noqa: BLE001
        return ""


def _record_path(pid: int) -> str:
    return os.path.join(AGENT_HOME, f"{RECORD_PREFIX}{pid}{RECORD_SUFFIX}")


def _own_stack_path(pid: int) -> str:
    return os.path.join(AGENT_HOME, f"{OWN_PREFIX}{pid}{OWN_SUFFIX}")


def _write_record() -> None:




    ensure_agent_directories()
    path = _record_path(os.getpid())
    fd, tmp = tempfile.mkstemp(prefix=".agent-", suffix=".tmp", dir=AGENT_HOME)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(_record, ensure_ascii=False))
        retry_file_op(os.replace, tmp, path)
    finally:
        remove_quietly(tmp)


def _update(**fields) -> None:

    try:
        if not _record:
            _open_record()
        _record.update(fields)
        _write_record()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Crash record not written: {type(exc).__name__}: {exc}")


def _open_record() -> None:

    _record.clear()
    try:
        with open(_record_path(os.getpid()), encoding="utf-8") as handle:
            old = json.loads(handle.read(8192))
        if isinstance(old, dict) and old.get("pid") == os.getpid():
            _record.update(old)
    except (OSError, ValueError):
        pass
    _record.setdefault("pid", os.getpid())
    _record.setdefault("started_at", time.time())
    _record.setdefault("run_id", "")
    _record.setdefault("session_id", "")
    _record["call"] = None
    _record["closed"] = False
    _record["qgis_version"] = _qgis_version()




def start() -> None:


    _update()
    _enable_stack_file()
    global _quit_signal
    try:
        from qgis.PyQt.QtCore import QCoreApplication

        app = QCoreApplication.instance()
        if app is not None and _quit_signal is None:
            app.aboutToQuit.connect(mark_closed)
            _quit_signal = app.aboutToQuit
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Crash record quit watch not set: {type(exc).__name__}: {exc}")


def stop() -> None:

    global _quit_signal
    if _quit_signal is not None:
        try:
            _quit_signal.disconnect(mark_closed)
        except Exception:  # nosec B110
            pass
        _quit_signal = None
    mark_closed()


def mark_closed() -> None:




    if _record:
        _update(closed=True, call=None)


def _enable_stack_file() -> None:



    try:
        import faulthandler

        if faulthandler.is_enabled():
            return
        ensure_agent_directories()
        handle = open(_own_stack_path(os.getpid()), "w", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(file=handle, all_threads=True)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Crash stack file not enabled: {type(exc).__name__}: {exc}")


def record_run(run_id: str, session_id: str) -> None:

    _update(run_id=str(run_id or ""), session_id=str(session_id or ""))


def write(run_id: str, tool_call_id: str, session_id: str, tool: str, args) -> None:

    _update(run_id=str(run_id or ""), session_id=str(session_id or "") or _record.get("session_id", ""),
            call={"tool_call_id": str(tool_call_id or ""), "tool": str(tool or ""),
                  "args_digest": args_digest(args), "started_at": time.time()})


def clear() -> None:

    _update(call=None)




def pid_alive(pid: int) -> bool:

    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return ctypes.get_last_error() == 5
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return True
                return code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _temp_dir() -> str:
    try:
        from qgis.PyQt.QtCore import QStandardPaths

        from .qt_compat import enum_member

        location = QStandardPaths.writableLocation(
            enum_member(QStandardPaths, "StandardLocation", "TempLocation"))
        if location:
            return os.path.normpath(location)
    except Exception:  # nosec B110
        pass
    return tempfile.gettempdir()


def _pid_of(name: str, prefix: str, suffix: str) -> int:
    if not name.startswith(prefix) or not name.endswith(suffix):
        return 0
    try:
        return int(name[len(prefix):len(name) - len(suffix)])
    except ValueError:
        return 0


def _stack_paths(pid: int) -> list:

    return [os.path.join(_temp_dir(), f"{STACK_PREFIX}{pid}"), _own_stack_path(pid)]


def take(plugin_version: str = "") -> dict | None:





    _pending.clear()
    try:
        names = os.listdir(AGENT_HOME)
    except OSError:
        return None
    now = time.time()
    drop: list = [os.path.join(AGENT_HOME, name) for name in _LEGACY if name in names]
    dead: list = []
    for name in names:
        pid = _pid_of(name, RECORD_PREFIX, RECORD_SUFFIX)
        if pid and not pid_alive(pid):
            path = os.path.join(AGENT_HOME, name)
            try:
                with open(path, encoding="utf-8") as handle:
                    record = json.loads(handle.read(8192))
                mtime = os.path.getmtime(path)
            except (OSError, ValueError):
                drop.append(path)
                continue

            if (not isinstance(record, dict) or record.get("closed") or now - mtime > MAX_AGE_S
                    or not record.get("run_id")):
                drop.extend([path, *_stack_paths(pid)])
                continue
            dead.append((mtime, pid, path, record))

        own = _pid_of(name, OWN_PREFIX, OWN_SUFFIX)
        if own and not pid_alive(own) and f"{RECORD_PREFIX}{own}{RECORD_SUFFIX}" not in names:
            drop.append(os.path.join(AGENT_HOME, name))
    dead.sort(key=lambda item: item[0], reverse=True)

    for _mtime, pid, path, _record in dead[1:]:
        drop.extend([path, *_stack_paths(pid)])
    _pending["drop"] = drop
    if not dead:
        return _legacy_note(names, plugin_version)
    mtime, pid, path, record = dead[0]
    _pending["drop"].extend([path, *_stack_paths(pid)])
    stack, stack_mtime = _stack_of(pid)
    call = record.get("call") if isinstance(record.get("call"), dict) else None
    note = {
        "run_id": str(record.get("run_id") or "")[:128],
        "session_id": str(record.get("session_id") or "")[:128],
        "qgis_version": str(record.get("qgis_version") or "")[:64],
        "plugin_version": str(plugin_version or "")[:64],
    }
    if call:
        log(f"QGIS had died inside {call.get('tool') or 'a tool call'}; reporting it")
        started = call.get("started_at")
        note.update({
            "tool_call_id": str(call.get("tool_call_id") or "")[:128],
            "tool": str(call.get("tool") or "")[:128],
            "args_digest": str(call.get("args_digest") or "")[:64],
            "started_at": float(started) if isinstance(started, (int, float)) else float(mtime),
        })
    else:
        log("QGIS had died after a call returned; reporting it")
        note.update({"tool_call_id": "", "tool": "", "args_digest": "", "after_call": True,
                     "started_at": float(stack_mtime or mtime)})
    if stack:
        note["python_stack"] = stack
    return note


def _legacy_note(names: list, plugin_version: str) -> dict | None:


    if "last_call.json" not in names:
        return None
    try:
        with open(os.path.join(AGENT_HOME, "last_call.json"), encoding="utf-8") as handle:
            note = json.loads(handle.read(4096))
    except (OSError, ValueError):
        return None
    started = note.get("started_at") if isinstance(note, dict) else None
    if (not isinstance(note, dict) or not note.get("run_id") or not isinstance(started, (int, float))
            or abs(time.time() - started) > MAX_AGE_S):
        return None
    log(f"QGIS had died inside {note.get('tool') or 'a tool call'}; reporting it")
    return {
        "run_id": str(note.get("run_id") or "")[:128],
        "tool_call_id": str(note.get("tool_call_id") or "")[:128],
        "session_id": str(note.get("session_id") or "")[:128],
        "tool": str(note.get("tool") or "")[:128],
        "args_digest": str(note.get("args_digest") or "")[:64],
        "qgis_version": str(note.get("qgis_version") or "")[:64],
        "plugin_version": str(note.get("plugin_version") or plugin_version or "")[:64],
        "started_at": float(started),
    }


def acknowledge() -> None:


    for path in _pending.get("drop", ()):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Crash file not removed: {type(exc).__name__}: {exc}")
    _pending.clear()




def _scrub(text: str) -> str:
    from .log_scrub import strip_paths

    text = strip_paths(text)
    try:
        import getpass

        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = ""
    if user and len(user) >= 2:

        text = re.sub(r"(?<![^\W_])" + re.escape(user) + r"(?![^\W_])", "<user>", text, flags=re.IGNORECASE)
    return text


def _stack_of(pid: int) -> tuple:

    for path in _stack_paths(pid):
        try:
            size = os.path.getsize(path)
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if size <= 0:
            continue
        try:
            with open(path, "rb") as handle:
                head = handle.read(512)
                if size > _STACK_READ_BYTES:
                    handle.seek(size - _STACK_READ_BYTES)
                else:
                    handle.seek(0)
                raw = handle.read(_STACK_READ_BYTES)
        except OSError as exc:
            log_warning(f"Crash stack not read: {type(exc).__name__}: {exc}")
            continue
        text = raw.decode("utf-8", "replace")
        if size > _STACK_READ_BYTES:
            text = head.decode("utf-8", "replace").split("\n", 1)[0] + "\n\n" + text.split("\n", 1)[-1]
        return shape_stack(_scrub(text)), mtime
    return "", 0.0


def shape_stack(text: str, limit: int = STACK_LIMIT) -> str:







    text = text.split("\nExtension modules:", 1)[0].strip()
    if len(text) <= limit:
        return text
    blocks = [block.strip("\n") for block in re.split(r"\n\s*\n", text) if block.strip()]
    cause = [b for b in blocks if not b.lstrip().startswith(("Thread ", "Current thread "))]
    threads = [b for b in blocks if b.lstrip().startswith("Thread ")]
    current = [b for b in blocks if b.lstrip().startswith("Current thread ")]
    head = "\n".join(cause)[:300]
    dying = current[-1] if current else (threads.pop() if threads else "")

    budget = limit - len(head) - 40
    lines = dying.split("\n")
    if len(dying) > budget:
        kept, used = [lines[0]], len(lines[0])
        frames = lines[1:]
        marker_room = 40
        for line in frames:
            if used + len(line) + 1 > budget - marker_room:
                break
            kept.append(line)
            used += len(line) + 1
        left = len(frames) - (len(kept) - 1)
        if left:
            kept.append(f"  ... {left} older frames left out")
        dying = "\n".join(kept)
    out = [head, dying] if head else [dying]
    room = limit - sum(len(part) + 2 for part in out)
    shown = []
    for block in reversed(threads):
        if len(block) + 2 + 40 > room:
            break
        shown.insert(0, block)
        room -= len(block) + 2
    left = len(threads) - len(shown)
    middle = ([f"[{left} other threads left out]"] if left else []) + shown
    parts = ([head] if head else []) + middle + [dying]
    return "\n\n".join(parts)[:limit]
