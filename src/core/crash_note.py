# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


































from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time

from . import freeze_stack
from .host_platform import qgis_version, remove_quietly, retry_file_op
from .log_scrub import strip_paths
from .logger import log, log_warning
from .numbers import as_int
from .policy import AGENT_HOME, ensure_agent_directories

RECORD_PREFIX = "process-"
RECORD_SUFFIX = ".json"
_LEGACY = ("last_call.json", "last_run.json")



MAX_AGE_S = 7 * 24 * 3600.0





STACK_PREFIX = "qgis-python-crash-info-"
OWN_PREFIX = "python-crash-"
OWN_SUFFIX = ".txt"



ALIVE_PREFIX = "alive-"
ALIVE_EVERY_MS = 60_000
STACK_LIMIT = 2000
_MAIN_ROOM = 400
_DUMP_START = re.compile(r"^(?:Fatal Python error|Windows fatal exception): ", re.MULTILINE)
_STACK_READ_BYTES = 262_144

_record: dict = {}
_alive_timer = None
_pending: dict = {}
_quit_signal = None
_session_end_signal = None
_quitting = False


def quitting() -> bool:

    return _quitting


def args_digest(args) -> str:

    try:
        text = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        text = str(args)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _record_path(pid: int) -> str:
    return os.path.join(AGENT_HOME, f"{RECORD_PREFIX}{pid}{RECORD_SUFFIX}")


def _own_stack_path(pid: int) -> str:
    return os.path.join(AGENT_HOME, f"{OWN_PREFIX}{pid}{OWN_SUFFIX}")


def _alive_path(pid: int) -> str:
    return os.path.join(AGENT_HOME, f"{ALIVE_PREFIX}{pid}")


def _touch_alive() -> None:
    try:
        with open(_alive_path(os.getpid()), "a", encoding="utf-8"):
            pass
        os.utime(_alive_path(os.getpid()), None)
    except OSError:
        pass


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
    _record.setdefault("main_thread", getattr(threading.main_thread(), "native_id", 0))
    _record.setdefault("started_at", time.time())
    _record.setdefault("run_id", "")
    _record.setdefault("session_id", "")
    _record["call"] = None
    _record["closed"] = False
    _record["qgis_version"] = qgis_version()


def start() -> None:


    _update()
    _enable_stack_file()
    global _quit_signal
    try:
        from qgis.PyQt.QtCore import QCoreApplication

        app = QCoreApplication.instance()
        if app is not None and _quit_signal is None:
            app.aboutToQuit.connect(_on_quit)
            _quit_signal = app.aboutToQuit
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Crash record quit watch not set: {type(exc).__name__}: {exc}")
    global _session_end_signal
    try:
        from qgis.PyQt.QtCore import QCoreApplication

        app = QCoreApplication.instance()
        if app is not None and _session_end_signal is None and hasattr(app, "commitDataRequest"):
            app.commitDataRequest.connect(_on_session_end)
            _session_end_signal = app.commitDataRequest
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Crash record session watch not set: {type(exc).__name__}: {exc}")
    global _alive_timer
    try:
        from qgis.PyQt.QtCore import QTimer

        if _alive_timer is None:
            _touch_alive()
            _alive_timer = QTimer()
            _alive_timer.setInterval(ALIVE_EVERY_MS)
            _alive_timer.timeout.connect(_touch_alive)
            _alive_timer.start()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Crash record alive mark not set: {type(exc).__name__}: {exc}")
    if os.name == "nt":
        threading.Thread(target=_note_windows_ends, name="terralab-crash-events", daemon=True).start()


def stop() -> None:

    global _alive_timer
    timer, _alive_timer = _alive_timer, None
    if timer is not None:
        try:
            timer.stop()
            timer.deleteLater()
        except RuntimeError:
            pass
    global _quit_signal
    if _quit_signal is not None:
        try:
            _quit_signal.disconnect(_on_quit)
        except Exception:  # nosec B110
            pass
        _quit_signal = None
    global _session_end_signal
    if _session_end_signal is not None:
        try:
            _session_end_signal.disconnect(_on_session_end)
        except Exception:  # nosec B110
            pass
        _session_end_signal = None
    mark_closed()


def _on_quit() -> None:
    global _quitting
    _quitting = True
    mark_closed()


def _on_session_end(*_manager) -> None:






    if _record:
        _update(session_end_at=time.time())


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


def record_session(session_id: str) -> None:


    if _record and session_id and session_id != _record.get("session_id"):
        _update(session_id=str(session_id))


def record_run(run_id: str, session_id: str, thread_id: str = "") -> None:


    _update(run_id=str(run_id or ""), session_id=str(session_id or ""), thread_id=str(thread_id or ""),
            session_end_at=None, run_end=None)


def record_run_end(run_id: str, status: str) -> None:


    if _record and run_id and run_id == _record.get("run_id"):
        _update(run_end={"status": str(status or "")[:20], "at": time.time()})


def write(run_id: str, tool_call_id: str, session_id: str, tool: str, args) -> None:

    _update(run_id=str(run_id or ""), session_id=str(session_id or "") or _record.get("session_id", ""),
            call={"tool_call_id": str(tool_call_id or ""), "tool": str(tool or ""),
                  "args_digest": args_digest(args), "started_at": time.time()},
            session_end_at=None)


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
    return as_int(name[len(prefix):len(name) - len(suffix)], 0)


def _stack_paths(pid: int) -> list:




    return [os.path.join(_temp_dir(), f"{STACK_PREFIX}{pid}"), _own_stack_path(pid), freeze_stack.path_for(pid),
            _alive_path(pid)]


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
                    or not (record.get("run_id") or record.get("session_id"))
                    or (record.get("session_end_at") and not _stack_of(pid)[0])):
                drop.extend([path, *_stack_paths(pid)])
                continue
            dead.append((mtime, pid, path, record))
        own = (_pid_of(name, OWN_PREFIX, OWN_SUFFIX) or freeze_stack.pid_of(name)
               or _pid_of(name, ALIVE_PREFIX, ""))
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
    _pending["chat"] = (str(record.get("thread_id") or ""), str(record.get("run_id") or ""))
    stack, stack_mtime = _stack_of(pid)
    call = record.get("call") if isinstance(record.get("call"), dict) else None
    note = {
        "run_id": str(record.get("run_id") or "")[:128],
        "session_id": str(record.get("session_id") or "")[:128],
        "qgis_version": str(record.get("qgis_version") or "")[:64],
        "plugin_version": str(plugin_version or "")[:64],
    }
    if not note["run_id"]:


        log("QGIS had died before opening a run; reporting it")
        started = record.get("started_at")
        note.update({"tool_call_id": "", "tool": "", "args_digest": "", "before_run": True,
                     "started_at": float(started) if isinstance(started, (int, float)) else float(mtime)})
    elif call:
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
    ended = record.get("run_end") if isinstance(record.get("run_end"), dict) else None
    if ended and isinstance(ended.get("at"), (int, float)):
        note["run_end_status"] = str(ended.get("status") or "")[:20]
        note["run_end_at"] = float(ended["at"])
    try:
        note["alive_at"] = float(os.path.getmtime(_alive_path(pid)))
    except OSError:
        pass
    if not stack:


        note.update({key: str(record[key])[:64] for key in _END_FIELDS if record.get(key)})
    window = _crash_window(pid, record)
    if window:
        note.setdefault("end_kind", "crash_window")
        note["end_thread"] = window
    if stack:
        note["python_stack"] = stack


        note["idle_s"] = round(max(0.0, stack_mtime - mtime), 1)
    return note


_END_FIELDS = ("end_kind", "end_code", "end_module")
CRASH_INFO_PREFIX = "qgis-crash-info-"


def _crash_window(pid: int, record: dict) -> str:








    path = os.path.join(_temp_dir(), f"{CRASH_INFO_PREFIX}{pid}")
    try:
        if os.path.getmtime(path) < float(record.get("started_at") or 0) - 2.0:
            return ""
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read(256).splitlines()
    except (OSError, ValueError):
        return ""
    if as_int(lines[0] if lines else "", 0) != pid:
        return ""
    thread, main = as_int(lines[1] if len(lines) > 1 else "", 0), as_int(record.get("main_thread"), 0)
    if not thread or not main:
        return "unknown"
    return "main" if thread == main else "other"
_EVENT_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"


def _note_windows_ends() -> None:




    try:
        records: dict = {}
        for name in os.listdir(AGENT_HOME):
            pid = _pid_of(name, RECORD_PREFIX, RECORD_SUFFIX)
            if not pid or pid == os.getpid() or pid_alive(pid):
                continue
            path = os.path.join(AGENT_HOME, name)
            with open(path, encoding="utf-8") as handle:
                record = json.loads(handle.read(8192))
            if (isinstance(record, dict) and not record.get("closed") and not record.get("session_end_at")
                    and not record.get("end_checked")):
                records[pid] = (path, record)
        if not records:
            return
        since = min(float(r.get("started_at") or 0) or time.time() for _p, r in records.values())
        ends = windows_ends(set(records), since)
        for pid, (path, record) in records.items():
            record.update(ends.get(pid, {}), end_checked=True)
            if os.path.exists(path):
                mtime = os.path.getmtime(path)
                fd, tmp = tempfile.mkstemp(prefix=".agent-", suffix=".tmp", dir=AGENT_HOME)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(json.dumps(record, ensure_ascii=False))
                    retry_file_op(os.replace, tmp, path)
                    os.utime(path, (mtime, mtime))
                finally:
                    remove_quietly(tmp)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Windows end of a dead QGIS not read: {type(exc).__name__}: {exc}")


def windows_ends(pids: set, since: float) -> dict:




    import subprocess  # nosec B404
    import xml.etree.ElementTree as ET  # nosec B405

    window_ms = int(min(MAX_AGE_S, max(3600.0, time.time() - since + 3600.0)) * 1000)
    query = f"*[System[(EventID=1000 or EventID=1002) and TimeCreated[timediff(@SystemTime) <= {window_ms}]]]"
    exe = os.path.join(os.environ.get("SYSTEMROOT") or r"C:\Windows", "System32", "wevtutil.exe")
    out = subprocess.run([exe, "qe", "Application", f"/q:{query}", "/f:xml", "/c:200", "/rd:true"],  # nosec B603
                         capture_output=True, timeout=20, check=False,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    found: dict = {}
    for event in ET.fromstring(b"<events>" + out + b"</events>"):  # nosec B314
        event_id = (event.findtext(f"{_EVENT_NS}System/{_EVENT_NS}EventID") or "").strip()
        data = event.findall(f"{_EVENT_NS}EventData/{_EVENT_NS}Data")
        named = {d.get("Name"): (d.text or "").strip() for d in data if d.get("Name")}
        values = [(d.text or "").strip() for d in data]

        def field(key: str, index: int, named: dict = named, values: list = values) -> str:

            return named.get(key) or (values[index] if len(values) > index else "")

        if event_id == "1000":
            app, proc = field("AppName", 0), field("ProcessId", 8)
        elif event_id == "1002":
            app, proc = (values[0] if values else ""), (values[2] if len(values) > 2 else "")
        else:
            continue
        try:
            pid = int(proc, 16)
        except ValueError:
            continue
        if pid not in pids or pid in found or "qgis" not in app.lower():
            continue
        if event_id == "1002":
            found[pid] = {"end_kind": "app_hang"}
            continue
        code = field("ExceptionCode", 6).lower()
        module = field("ModuleName", 3).replace("\\", "/")
        found[pid] = {"end_kind": "app_error", "end_code": code if code.startswith("0x") else "0x" + code,
                      "end_module": os.path.basename(module)[:64]}
    return found


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


def taken_chat() -> tuple:

    return _pending.get("chat") or ("", "")


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


def _cut_block(block: str, room: int) -> str:

    if len(block) <= room:
        return block
    lines = block.split("\n")
    kept, used = [lines[0]], len(lines[0])
    frames = lines[1:]
    marker_room = 40
    for line in frames:
        if used + len(line) + 1 > room - marker_room:
            break
        kept.append(line)
        used += len(line) + 1
    left = len(frames) - (len(kept) - 1)
    if left:
        kept.append(f"  ... {left} older frames left out")
    return "\n".join(kept)


def shape_stack(text: str, limit: int = STACK_LIMIT) -> str:










    text = text.split("\nExtension modules:", 1)[0].strip()
    dumps = [found.start() for found in _DUMP_START.finditer(text)]
    if dumps:
        text = text[dumps[-1]:]
    if len(text) <= limit:
        return text
    blocks = [block.strip("\n") for block in re.split(r"\n\s*\n", text) if block.strip()]
    cause = [b for b in blocks if not b.lstrip().startswith(("Thread ", "Current thread "))]
    threads = [b for b in blocks if b.lstrip().startswith(("Thread ", "Current thread "))]
    head = "\n".join(cause)[:300]
    last = threads.pop() if threads else ""
    current = [b for b in threads if b.lstrip().startswith("Current thread ")]

    room = limit - len(head) - 40
    if current:
        threads.remove(current[-1])
        main = _cut_block(last, _MAIN_ROOM)
        ends = [_cut_block(current[-1], room - len(main)), main]
    else:
        ends = [_cut_block(last, room)]
    room = limit - sum(len(part) + 2 for part in ([head] if head else []) + ends)
    shown = []
    for block in reversed(threads):
        if len(block) + 2 + 40 > room:
            break
        shown.insert(0, block)
        room -= len(block) + 2
    left = len(threads) - len(shown)
    middle = ([f"[{left} other threads left out]"] if left else []) + shown
    return "\n\n".join(([head] if head else []) + middle + ends)[:limit]
